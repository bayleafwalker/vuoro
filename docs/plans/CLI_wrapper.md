---
doc_id: vuoro-cli-output-compaction-wrapper
lifecycle: draft
---

# Semantic Tool-Output Compaction Wrapper

## Status

Draft experiment proposal. The hypothesis and candidate design are useful, but
implementation is not authorized by this document or ready for `dispatch-build`.
Ownership, evidence-storage/redaction semantics, stream ordering, and the pilot
oracle still require a planning decision.

## 2026-07-29 assessment

This proposal is adjacent to Vuoro, not currently part of Vuoro's owned
runtime. Vuoro owns transport-only client behavior and reusable service
composition; it does not own agent-harness command execution, Kubernetes
operations, or a general tool-output store. The initial wrapper therefore must
not be added to `vuoro-client` or `vuoro-service` unless a later ratified
decision identifies a narrow Vuoro-owned integration surface. The likely owner
is the repository selected to own reusable agent execution mediation; that
selection must be reconciled with Agentops' dispatch/tooling ownership and
Actionq's execution ownership before implementation.

The proposed delivery is split into the following dispositions:

| Candidate | Disposition | Reason |
|---|---|---|
| Ownership, trust boundary, and experiment contract | plan now | Blocks every implementation phase and determines the home repository. |
| Phase 0 runner and evidence retrieval | build after decision | Bounded only after subprocess, signal, stream-order, permissions, retention, and redaction behavior are frozen. |
| Deterministic generic and `kubectl logs` compaction | build after Phase 0 proof | The recommended first experiment; no model dependency is needed. |
| A/B pilot | plan with the implementation oracle | Scenario reset, correctness scoring, and token accounting must be independently reproducible and must not authorize cluster mutation. |
| Cheap-model extraction | defer | It confounds the deterministic-compaction hypothesis and expands the untrusted-input boundary. |
| Transparent `kubectl get` supplementation | defer pending explicit consent semantics | It executes an additional command and therefore violates the primary goal of preserving arbitrary-command behavior if done silently. |
| Cross-run caching, MCP integration, routing, and additional profiles | park | These are future product directions, not MVP acceptance requirements. |

The next planning pass must produce:

1. a named owning repository and package boundary;
2. a versioned `v1` result schema and explicit compatibility policy;
3. byte-level stdout/stderr capture semantics plus a deterministic ordering
   model for any combined view;
4. fail-closed file permissions, retention, redaction, and deletion behavior,
   including whether raw evidence may leave the execution host;
5. cancellation, timeout, and wrapped-command exit-status rules;
6. a falsifying test oracle for command transparency, evidence recovery,
   decisive-line recall, and unsupported-inference rejection; and
7. a pilot environment whose reset and read-only diagnostic permissions are
   owned outside this document.

Until those decisions land, examples below are illustrative candidate
contracts, not shipped commands or stable public interfaces.

## Summary

Build a minimal CLI wrapper that runs commands, preserves their complete raw output, and emits a compact, structured representation intended for use by coding agents.

The initial target is Kubernetes diagnostics through `kubectl`, where command output can be extremely large while the decision-relevant information may consist of only a handful of lines.

The purpose of the first implementation is not to build a general agent router or orchestration framework. It is to test one hypothesis:

> Domain-aware compaction of tool output allows an agent to diagnose operational failures with fewer tokens, fewer repeated tool calls, and no meaningful reduction in correctness.

The wrapper should be deliberately small, stateless where practical, measurable, and easy to bypass.

---

# Problem

Coding-agent harnesses commonly expose command output to the model as text.

For large outputs, the harness may:

* return the entire output;
* truncate the output;
* retain only the beginning or end;
* ask the model to inspect the output directly;
* compact earlier context later in the session.

These mechanisms are generic. They do not necessarily understand that:

* the relevant Kubernetes error may appear once in 10,000 log lines;
* repeated stack traces are mostly duplicate information;
* restart counts, pod conditions, probe failures, and recent events are more important than routine log traffic;
* the full output may still be needed later for verification or forensic inspection.

Naive truncation reduces token use but can remove the decisive evidence.

Passing the entire output preserves evidence but pollutes the agent context and may cause repeated searches, weak attention allocation, and unnecessary tool calls.

The proposed wrapper introduces a semantic boundary between command execution and agent context.

---

# Goals

## Primary goals

1. Execute an arbitrary command without changing its behavior.
2. Store the complete stdout and stderr outside the model context.
3. Extract high-signal observations using deterministic logic where possible.
4. Optionally use a cheap model for bounded semantic extraction.
5. Return compact human-readable output and machine-readable JSON.
6. Preserve pointers back to the raw evidence.
7. Measure whether the wrapper improves agent efficiency.

## Secondary goals

* Support additional command families through pluggable extractors.
* Allow the agent to request targeted retrieval from the stored raw output.
* Detect repeated or highly similar output.
* Make compaction behavior explicit and inspectable.
* Support deterministic replay of an earlier run.

## Non-goals for the MVP

* General-purpose agent routing.
* Autonomous planning.
* Choosing which expensive model should handle a task.
* Replacing observability platforms.
* Long-term semantic memory.
* Distributed execution.
* A generic workflow engine.
* Automatic remediation.
* Perfect log interpretation.

The wrapper may later become part of a larger mediation layer, but the first implementation should not become a shadow agent.

---

# Working Name

Examples:

* `toolcompact`
* `cmdcompact`
* `ctxrun`
* `signal`
* `kcompact`

For the examples below, the binary is called `ctxrun`.

---

# Core Usage

```bash
ctxrun -- kubectl logs deployment/payment-api -n payments
```

Compact output:

```text
Command succeeded with diagnostic findings.

Findings:
- 3 pods show repeated PostgreSQL connection failures.
- Primary error: connection refused to postgres.payments.svc:5432.
- First observed: 2026-07-29T07:41:12Z.
- Last observed: 2026-07-29T07:44:58Z.
- 847 repeated instances were collapsed.
- No application panic or OOM signature detected.

Raw output:
.ctxrun/runs/01J4.../combined.log

Run ID:
01J4...
```

Machine-readable mode:

```bash
ctxrun --format json -- kubectl logs deployment/payment-api -n payments
```

```json
{
  "schema_version": "v1",
  "run_id": "01J4...",
  "command": [
    "kubectl",
    "logs",
    "deployment/payment-api",
    "-n",
    "payments"
  ],
  "exit_code": 0,
  "duration_ms": 1842,
  "raw": {
    "stdout_path": ".ctxrun/runs/01J4.../stdout.log",
    "stderr_path": ".ctxrun/runs/01J4.../stderr.log",
    "combined_path": ".ctxrun/runs/01J4.../combined.log",
    "stdout_bytes": 842193,
    "stderr_bytes": 0,
    "sha256": "..."
  },
  "compaction": {
    "strategy": "kubectl-logs-v1",
    "model_used": false,
    "input_lines": 10482,
    "output_tokens_estimated": 162,
    "truncated": false
  },
  "findings": [
    {
      "type": "connection_failure",
      "severity": "error",
      "summary": "PostgreSQL connection refused",
      "count": 847,
      "first_seen": "2026-07-29T07:41:12Z",
      "last_seen": "2026-07-29T07:44:58Z",
      "evidence": [
        {
          "path": ".ctxrun/runs/01J4.../combined.log",
          "line_start": 84,
          "line_end": 84
        }
      ]
    }
  ],
  "observations": [
    "The error occurs across three pods.",
    "The destination is postgres.payments.svc:5432."
  ],
  "uncertainties": [
    "The wrapper did not verify whether the PostgreSQL Service has ready endpoints."
  ],
  "suggested_next_queries": [
    [
      "kubectl",
      "get",
      "endpoints",
      "postgres",
      "-n",
      "payments",
      "-o",
      "yaml"
    ],
    [
      "kubectl",
      "get",
      "pods",
      "-n",
      "payments",
      "-l",
      "app=postgres",
      "-o",
      "wide"
    ]
  ]
}
```

---

# Design Principle

The wrapper must distinguish between four categories:

## Evidence

Raw command output or exact excerpts.

Evidence must remain recoverable and must not be rewritten as fact.

## Observation

A directly extractable statement supported by evidence.

Example:

> Container `api` restarted 12 times.

## Inference

A likely interpretation that is not directly proven.

Example:

> The application may be starting before the database is reachable.

## Recommendation

A proposed next action.

Example:

> Inspect the PostgreSQL Service endpoints.

These categories must not be collapsed into an undifferentiated prose summary.

---

# Proposed Architecture

```text
Agent
  |
  v
ctxrun CLI
  |
  +--> Command runner
  |      |
  |      +--> stdout/stderr capture
  |      +--> exit code
  |      +--> timing
  |      +--> raw artifact storage
  |
  +--> Command classifier
  |
  +--> Deterministic extractor
  |      |
  |      +--> kubectl logs
  |      +--> kubectl get/describe
  |      +--> generic text
  |
  +--> Optional cheap-model extractor
  |
  +--> Normalizer and evidence linker
  |
  +--> Human output
  +--> JSON output
  +--> Metrics record
```

---

# Processing Pipeline

## 1. Execute

Run the requested command as a child process.

Requirements:

* preserve the argument vector;
* do not execute through a shell unless explicitly requested;
* capture stdout and stderr separately;
* optionally stream output to the terminal;
* retain the original exit code;
* handle cancellation and signals correctly;
* record start time, finish time, and duration.

## 2. Persist raw output

Store raw output before attempting semantic processing.

Suggested layout:

```text
.ctxrun/
  runs/
    <run-id>/
      metadata.json
      stdout.log
      stderr.log
      combined.log
      compact.json
      compact.txt
```

Do not delete raw output automatically in the initial implementation.

Add retention controls later.

## 3. Classify the command

Example classifier output:

```json
{
  "family": "kubectl",
  "operation": "logs",
  "confidence": 1.0
}
```

Classification should initially be deterministic and argument-based.

Do not use an LLM to determine that `kubectl logs` is a `kubectl logs` command. That would be an expensive way to rediscover spelling.

## 4. Apply deterministic extraction

Examples for `kubectl logs`:

* detect timestamps;
* group repeated or near-identical messages;
* extract log levels;
* retain first and last occurrence;
* identify Kubernetes and common runtime signatures;
* detect panic, traceback, fatal, OOM, timeout, connection refusal, DNS failure, TLS failure, permission denial, and probe failure patterns;
* detect changes near the end of the log;
* retain rare high-severity lines;
* retain surrounding context for anomalies;
* identify pod or container prefixes when present.

Examples for `kubectl describe`:

* parse status and conditions;
* extract restart counts;
* extract image and image pull failures;
* extract readiness and liveness failures;
* retain warning events;
* collapse duplicate events;
* identify scheduling failures;
* identify volume mount failures;
* identify resource pressure or eviction signals.

Examples for `kubectl get ... -o json`:

* parse JSON directly;
* do not send raw JSON through an LLM;
* extract conditions, status, restart counts, owners, node placement, images, resource requests, and relevant timestamps.

## 5. Decide whether model extraction is needed

The cheap model should be a fallback or enrichment stage, not the default parser.

Possible activation conditions:

* no deterministic findings were produced;
* unclassified free-form logs exceed a size threshold;
* the output contains high-entropy errors not covered by known rules;
* multiple symptoms need semantic grouping;
* the user explicitly requests semantic interpretation.

The model input should be bounded.

Do not send all 10,000 lines merely because the wrapper owns a cheaper model.

Instead provide:

* rare error lines;
* clustered representative samples;
* first and last instances;
* surrounding context;
* command metadata;
* deterministic findings;
* line references.

## 6. Normalize findings

Convert parser and model output into a common schema.

Every important finding should include evidence references.

## 7. Emit compact output

Support at least:

```bash
--format text
--format json
```

Optional later:

```bash
--format jsonl
--format markdown
```

## 8. Record metrics

Each invocation should record:

* raw byte count;
* raw line count;
* estimated raw token count;
* compact byte count;
* estimated compact token count;
* execution duration;
* compaction duration;
* extractor used;
* model used;
* estimated model cost;
* number of findings;
* whether the agent later requested raw evidence.

---

# Minimal CLI Surface

```text
ctxrun [OPTIONS] -- COMMAND [ARGUMENTS...]

Options:
  --format text|json
  --profile auto|generic|kubectl-logs|kubectl-status
  --raw-dir PATH
  --max-summary-tokens N
  --model off|auto|MODEL
  --stream
  --no-stream
  --keep-raw
  --no-keep-raw
  --timeout DURATION
```

Additional retrieval commands:

```bash
ctxrun show <run-id>
ctxrun raw <run-id>
ctxrun excerpt <run-id> --lines 800:850
ctxrun grep <run-id> 'connection refused'
```

The retrieval functionality matters. Without it, the summary becomes a lossy dead end.

---

# Suggested JSON Schema

```json
{
  "schema_version": "v1",
  "run_id": "string",
  "timestamp": "RFC3339",
  "command": ["string"],
  "working_directory": "string",
  "exit_code": 0,
  "duration_ms": 0,
  "classification": {
    "family": "string",
    "operation": "string",
    "confidence": 0.0
  },
  "raw": {
    "stdout_path": "string",
    "stderr_path": "string",
    "combined_path": "string",
    "stdout_bytes": 0,
    "stderr_bytes": 0,
    "line_count": 0,
    "estimated_tokens": 0,
    "sha256": "string"
  },
  "compaction": {
    "strategy": "string",
    "model_used": false,
    "model": null,
    "duration_ms": 0,
    "estimated_tokens": 0,
    "truncated": false
  },
  "findings": [
    {
      "id": "F001",
      "type": "string",
      "severity": "debug|info|warning|error|critical",
      "summary": "string",
      "count": 1,
      "first_seen": "RFC3339 or null",
      "last_seen": "RFC3339 or null",
      "attributes": {},
      "evidence": [
        {
          "stream": "stdout|stderr|combined",
          "path": "string",
          "line_start": 0,
          "line_end": 0,
          "sha256": "string"
        }
      ]
    }
  ],
  "observations": ["string"],
  "inferences": [
    {
      "statement": "string",
      "confidence": "low|medium|high",
      "based_on": ["F001"]
    }
  ],
  "uncertainties": ["string"],
  "suggested_next_queries": [
    ["string"]
  ]
}
```

---

# Kubernetes MVP Scope

Implement only these initial cases.

## Profile 1: `kubectl logs`

Recognize:

```bash
kubectl logs ...
kubectl logs -f ...
kubectl logs --previous ...
kubectl logs -l ...
```

Minimum functionality:

* count input lines;
* identify error-like lines;
* group exact duplicates;
* normalize timestamps, pod names, request IDs, UUIDs, and numeric values for grouping;
* preserve representative evidence;
* retain the final 20 to 50 lines;
* retain lines around abrupt severity changes;
* report repeated signatures and their counts;
* report first and last occurrence;
* detect common operational signatures.

Suggested initial signatures:

* `connection refused`
* `connection reset`
* `context deadline exceeded`
* `i/o timeout`
* `no such host`
* `temporary failure in name resolution`
* `certificate`
* `x509`
* `unauthorized`
* `forbidden`
* `permission denied`
* `out of memory`
* `OOMKilled`
* `panic`
* `fatal`
* `segmentation fault`
* `traceback`
* `exception`
* `crashloop`
* `readiness probe failed`
* `liveness probe failed`

## Profile 2: `kubectl get`

Prefer structured output.

The wrapper may rewrite eligible read-only commands internally to request JSON, provided this behavior is explicit and recorded.

For example:

```bash
kubectl get pods -n payments
```

could optionally be supplemented with:

```bash
kubectl get pods -n payments -o json
```

Do not silently change mutating commands.

Extract:

* phase;
* readiness;
* restart count;
* age;
* deletion timestamp;
* pod conditions;
* container state;
* last termination reason;
* node;
* owner;
* image.

## Profile 3: `kubectl describe`

Extract:

* object identity;
* current state;
* conditions;
* container state and last state;
* restart counts;
* probe failures;
* scheduling failures;
* warning events;
* volume errors;
* image pull errors;
* resource constraints.

---

# Cheap-Model Contract

The cheap model must receive a strict instruction and return schema-valid JSON.

Suggested responsibilities:

* cluster semantically similar error samples;
* identify likely causal chains;
* separate symptoms from probable causes;
* describe what remains uncertain;
* suggest a small number of targeted next commands.

The model must not:

* claim it inspected omitted raw lines;
* invent evidence;
* produce unsupported certainty;
* recommend mutating commands by default;
* output large prose summaries;
* replace deterministic facts with paraphrases.

Example model input:

```json
{
  "command": ["kubectl", "logs", "deployment/api", "-n", "payments"],
  "deterministic_findings": [],
  "samples": [
    {
      "line_start": 120,
      "line_end": 124,
      "text": "..."
    }
  ],
  "tail": [
    {
      "line_start": 10460,
      "line_end": 10482,
      "text": "..."
    }
  ]
}
```

The model should return only:

```json
{
  "findings": [],
  "inferences": [],
  "uncertainties": [],
  "suggested_next_queries": []
}
```

Validate the response before merging it.

On invalid output, fall back to deterministic results.

---

# Safety and Trust Requirements

## Preserve evidence

The raw output is the source of truth.

Compact output is an index and interpretation layer.

## Expose uncertainty

The wrapper should say:

> Service endpoints were not inspected.

It should not say:

> The Service has no endpoints.

unless evidence proves that claim.

## Avoid command injection

Log content is untrusted data.

The model prompt must explicitly treat all log text as data, not instructions.

Suggested delimiter:

```text
BEGIN_UNTRUSTED_TOOL_OUTPUT
...
END_UNTRUSTED_TOOL_OUTPUT
```

Never execute commands suggested inside logs.

## Limit automatic next actions

The MVP may recommend read-only diagnostic commands.

It should not automatically execute them unless the caller explicitly enables that behavior.

## Redact secrets

Provide an optional redaction pass for:

* bearer tokens;
* API keys;
* passwords in URLs;
* Kubernetes Secret data;
* authorization headers;
* common cloud credentials.

Raw files may also contain secrets, so file permissions should default to user-only.

```text
0600 files
0700 directories
```

## Preserve exit semantics

The wrapper should exit with the wrapped command's exit code unless the wrapper itself fails before or during execution.

A separate metadata field should represent compaction failure.

---

# Failure Modes

## False-negative compaction

The wrapper omits the decisive line.

Mitigation:

* retain raw output;
* retain tail lines;
* retain rare high-severity lines;
* provide grep and excerpt access;
* measure how often agents request raw output;
* expose compaction confidence.

## False-positive diagnosis

The cheap model invents a causal explanation.

Mitigation:

* separate observation from inference;
* require evidence references;
* label confidence;
* deterministic extraction first;
* schema validation;
* allow model-free operation.

## Over-compaction

The output is too terse to support planning.

Mitigation:

* configurable token budget;
* structured findings rather than one paragraph;
* targeted follow-up retrieval;
* include unresolved questions.

## Under-compaction

The wrapper returns thousands of “important” lines.

Mitigation:

* cap representatives per signature;
* group duplicates;
* prioritize rare and severe events;
* return counts rather than instances.

## Wrapper becomes a planner

The tool begins deciding the overall troubleshooting strategy.

Mitigation:

* restrict it to execution, extraction, evidence indexing, and bounded recommendations;
* keep orchestration in the primary agent;
* do not maintain hidden task state.

## Double compaction

The wrapper compacts output, and the harness later compacts it again.

Mitigation:

* emit stable structured JSON;
* keep summaries concise;
* include run IDs and evidence pointers;
* avoid unnecessary narrative text.

---

# A/B Evaluation Plan

## Objective

Compare agent performance with and without the wrapper.

## Fixed variables

Use the same:

* cluster state;
* injected failure;
* task prompt;
* model;
* model configuration;
* agent harness;
* repository state;
* tool permissions;
* time budget.

The only intended variable is whether the agent is instructed to use `ctxrun`.

## Test groups

### Control

The agent uses ordinary commands:

```bash
kubectl logs ...
kubectl describe ...
kubectl get ...
```

### Treatment

The agent is instructed to use:

```bash
ctxrun -- kubectl logs ...
ctxrun -- kubectl describe ...
ctxrun -- kubectl get ...
```

Raw access remains available through `ctxrun raw`, `grep`, and `excerpt`.

## Suggested failure scenarios

1. Application cannot resolve a dependency through DNS.
2. Database Service has no ready endpoints.
3. Readiness probe points to the wrong port.
4. Container is OOMKilled.
5. Image pull fails because of an invalid tag.
6. PVC cannot mount.
7. NetworkPolicy blocks egress.
8. TLS certificate has expired.
9. Deployment uses an invalid ConfigMap key.
10. Logs contain large volumes of routine traffic with one rare fatal error.

Include both simple and multi-cause failures.

## Repetition

Run each scenario multiple times because agent behavior is stochastic.

Suggested minimum:

```text
10 scenarios
x 5 runs
x 2 groups
= 100 agent runs
```

A smaller pilot can use three scenarios and three repetitions.

## Metrics

### Efficiency

* total input tokens;
* total output tokens;
* total model cost;
* wall-clock time;
* number of tool calls;
* number of log commands;
* number of repeated or near-identical commands;
* bytes of raw tool output exposed to the primary model;
* compacted tokens exposed to the primary model;
* cheap-model token use;
* cheap-model cost.

### Quality

* correct root cause identified;
* decisive evidence cited;
* correct remediation proposed;
* harmful remediation proposed;
* premature conclusion;
* unresolved task at timeout;
* unnecessary cluster mutation;
* raw-log fallback required.

### Behavioral indicators

* repeated reading of the same logs;
* searching logs with `grep`, `tail`, or `sed`;
* agent asks for broader output after truncation;
* agent becomes anchored on an early misleading line;
* agent ignores an important late line;
* agent spends turns narrating rather than diagnosing.

## Evaluation output

Store one result record per run:

```json
{
  "scenario": "database-no-endpoints",
  "group": "treatment",
  "model": "example-model",
  "success": true,
  "root_cause_correct": true,
  "total_input_tokens": 0,
  "total_output_tokens": 0,
  "tool_calls": 0,
  "raw_tool_bytes_exposed": 0,
  "wrapper_model_tokens": 0,
  "wall_time_seconds": 0,
  "repeated_tool_calls": 0,
  "raw_fallbacks": 0,
  "notes": ""
}
```

---

# Success Criteria

The experiment should be considered promising if the treatment group achieves all of the following:

* at least 25% lower primary-model input-token use;
* no statistically or operationally meaningful reduction in diagnosis accuracy;
* fewer repeated log retrievals;
* no increase in harmful remediation;
* acceptable added latency;
* cheap-model cost substantially below the primary-model savings;
* raw fallback required in a minority of cases.

The exact thresholds can be adjusted, but diagnosis quality must not be traded away merely to produce attractive token graphs.

A wrapper that is cheap but confidently wrong is just automated gaslighting with JSON output.

---

# Implementation Recommendation

## Language

Recommended: Go.

Reasons:

* simple single-binary distribution;
* good subprocess and signal handling;
* straightforward streaming I/O;
* easy static builds;
* appropriate for a CLI used inside agent environments;
* good Kubernetes ecosystem compatibility.

Rust is also suitable but likely adds implementation friction without improving the experiment.

Python is acceptable for a very fast prototype, especially if model SDK integration dominates. It is less attractive as the eventual transparent command wrapper.

## Suggested packages

```text
cmd/
  ctxrun/
internal/
  runner/
  storage/
  classify/
  compact/
    generic/
    kubectl/
  model/
  schema/
  metrics/
```

Avoid a plugin framework in the first version. Use a small interface and compile extractors into the binary.

Example:

```go
type Extractor interface {
    Match(command []string) bool
    Extract(ctx context.Context, run RunArtifacts) (CompactResult, error)
}
```

---

# Implementation Phases

## Phase 0: Baseline capture

Implement:

* command execution;
* stdout and stderr capture;
* raw artifact storage;
* text and JSON metadata output;
* token estimation;
* run IDs;
* exit-code preservation.

No compaction yet.

This validates the wrapper semantics before adding intelligence.

## Phase 1: Generic compaction

Implement:

* line counting;
* exact duplicate grouping;
* normalized duplicate grouping;
* severity detection;
* head and tail retention;
* error-context extraction;
* evidence line references.

No model integration.

## Phase 2: Kubernetes extractors

Implement:

* `kubectl logs`;
* `kubectl get ... -o json`;
* `kubectl describe`;
* Kubernetes-specific signatures;
* structured status extraction.

## Phase 3: Cheap-model fallback

Implement:

* model interface;
* bounded sample selection;
* strict JSON schema;
* prompt-injection boundaries;
* timeout and failure fallback;
* cost and token metrics.

Support one provider initially.

## Phase 4: Evaluation harness

Implement:

* reproducible failure injection;
* scenario reset;
* agent-run metadata capture;
* group assignment;
* result aggregation;
* comparison report.

## Phase 5: Targeted retrieval

Implement:

```bash
ctxrun grep
ctxrun excerpt
ctxrun raw
ctxrun show
```

This may move earlier if agents require raw fallback during pilot testing.

---

# Candidate backlog

This list decomposes the draft design; it is not live execution authority.
Sprintctl remains authoritative for accepted work. Items should be promoted
from this list only after the ownership and experiment-contract planning gate
above is resolved, with dependencies and repository-local acceptance evidence.

## P0

* [ ] Define `v1` output schema.
* [ ] Implement command execution without shell interpolation.
* [ ] Preserve signals and exit codes.
* [ ] Persist stdout and stderr.
* [ ] Generate run IDs.
* [ ] Emit text and JSON output.
* [ ] Implement exact duplicate grouping.
* [ ] Implement error-line extraction.
* [ ] Implement evidence line references.
* [ ] Implement `kubectl logs` classifier.
* [ ] Add integration tests for large outputs.
* [ ] Add token and byte metrics.

## P1

* [ ] Normalize timestamps and request IDs for grouping.
* [ ] Parse Kubernetes JSON output.
* [ ] Parse common `kubectl describe` sections.
* [ ] Implement secret redaction.
* [ ] Implement raw retrieval subcommands.
* [ ] Add cheap-model interface.
* [ ] Add strict model-output validation.
* [ ] Add A/B scenario harness.

## P2

* [ ] Similarity-based deduplication.
* [ ] Cross-run signature caching.
* [ ] Configurable retention.
* [ ] Additional profiles such as `pytest`, Terraform, and GitHub Actions.
* [ ] OpenTelemetry export.
* [ ] Agent-protocol or MCP integration.
* [ ] Policy-based execution routing.

---

# Test Cases

## Command behavior

* command with no output;
* command with stdout only;
* command with stderr only;
* command with mixed output;
* non-zero exit;
* process killed by signal;
* wrapper interrupted;
* output larger than available memory;
* binary output;
* invalid executable;
* timeout.

## Compaction behavior

* one error in 10,000 routine lines;
* error only at the beginning;
* error only at the end;
* repeated stack trace;
* multiple distinct errors;
* timestamps on every line;
* request ID on every line;
* multiline traceback;
* JSON logs;
* malformed JSON logs;
* Unicode output;
* extremely long individual line;
* output containing prompt-injection text.

## Kubernetes behavior

* `CrashLoopBackOff`;
* `OOMKilled`;
* readiness probe failure;
* image pull failure;
* DNS failure;
* Service without endpoints;
* scheduling failure;
* PVC mount failure;
* expired certificate;
* normal healthy deployment.

---

# Agent Instructions for the Treatment Group

Use the following system or repository instruction:

```text
For potentially large diagnostic command output, use `ctxrun` instead of
running the command directly.

Example:

    ctxrun -- kubectl logs deployment/example -n example

Treat the compact result as an index of findings, not as complete evidence.
Use `ctxrun grep`, `ctxrun excerpt`, or `ctxrun raw` when exact source material
is needed.

Prefer structured Kubernetes queries such as JSON output. Do not bypass
`ctxrun` merely to obtain a larger unfiltered output unless the compact result
is insufficient.
```

Do not add stronger wording that forces the agent to trust the summary unconditionally.

---

# Open Questions

1. Should raw output be stored locally, in object storage, or both?
2. Should `kubectl get` commands be transparently supplemented with JSON queries?
3. How should line references work when stdout and stderr are interleaved?
4. What token estimator should be used across different primary models?
5. Should model extraction happen synchronously or only when deterministic confidence is low?
6. How should secrets be handled in raw retained files?
7. Should identical output across repeated runs reuse earlier compaction?
8. Is the compact JSON best returned directly to the model, or rendered as concise text?
9. How often does the agent need raw fallback?
10. Does compaction improve success, or merely reduce visible token accounting?

The last question is the experiment’s actual point.

---

# Future Direction

If the experiment succeeds, the wrapper can evolve into a general tool-output mediation layer.

Potential actions:

```text
PASS_THROUGH
PARSE
COMPACT
CHUNK_AND_REDUCE
STORE_AND_REFERENCE
ESCALATE
REJECT
```

Potential profiles:

* Kubernetes;
* test runners;
* compilers;
* Terraform;
* GitHub Actions;
* application logs;
* SQL query plans;
* cloud audit logs;
* package-manager output.

At that stage, routing may be added, but it should remain policy-driven and observable.

The primary agent should continue to own planning. The mediation layer should own the shape, provenance, and retrieval of tool information.

---

# Recommended First Deliverable

Build one binary supporting:

```bash
ctxrun -- kubectl logs ...
ctxrun --format json -- kubectl logs ...
ctxrun raw <run-id>
ctxrun grep <run-id> <pattern>
```

Use deterministic extraction only.

Run the first A/B pilot before adding a model.

This establishes whether semantic filtering itself has value. Adding a cheap model too early would make it difficult to determine whether improvements came from the architecture or merely from inserting another capable model into the loop.

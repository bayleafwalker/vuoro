# vuoro-reconciler

The trusted-side executor for E3 (agentops#2467) effect intents. It is the
only thing in the E2/E3 design that applies a diff, signs a commit or opens
a pull request: `packages/vuoro-mcp-edge`'s `propose_effect` only records a
proposal and has no credential and no route to this package (see
`vuoro_mcp_edge.effect_tools`'s module docstring and
`docs/plans/2026-09-26-e2-e3-shared-contract.md` section 7).

## Acceptance (TS-16)

A cloud caller never applies an effect; it only queues one. `propose_effect`
records an intent in state `proposed`, and `proposed -> accepted` is done
only on the trusted side, by an actor that is never the proposing principal
and never reachable through any edge/MCP tool:

- **Operator (default).** `vuoro-reconciler accept <intent_id> --operator
  <subject> --intent-source module:factory` shows the intent and its diff,
  asks for confirmation, and records `{kind: "operator", subject}`.
  `vuoro-reconciler reject <intent_id> ... --reason <text>` records a
  rejection. Accepting as the proposer is refused.
- **Auto-accept policy (opt-in, off by default).** `AutoAcceptConfig(path)`
  reads a trusted-side JSON file passed explicitly -- never the environment,
  never a cloud-callable tool:
  `{version, policies: [{id, enabled, workspace_id, repository?,
  effect_kinds: [...], path_globs?}]}`. A missing or empty file means off.
  `Reconciler(auto_accept=...)` evaluates it asynchronously at the start of
  each `run_once`, and records `{kind: "policy", policy_id, version, scope,
  config_digest}` as the acceptor.

## What it does

1. Applies auto-accept (if configured) to `poll_proposed()`, then polls
   accepted intents through the `IntentSource` protocol. An accepted intent
   without an acceptor, or accepted by its own proposer, is refused.
2. Clones the intent's repository (with `core.hooksPath=/dev/null`) at a
   clean checkout of `base_commit` and applies `unified_diff`. A diff that
   does not apply cleanly is refused before anything is committed.
3. Re-validates what the diff actually changed against the reconciler's
   own `DiffPolicy` (binary, mode changes including new executable files,
   symlinks, submodules, deletes outside the path allowlist, CI workflow
   and protected paths), and a policy acceptor's recorded scope -- it does
   not trust the edge's validation.
4. Commits the result, signed by the reconciler's own key and committer
   identity (SSH or GPG, injected at runtime -- this package never mints or
   stores one), with trailers `Vuoro-Run: <run_id>`, `Vuoro-Intent:
   <intent_id>` and `Vuoro-Accepted-By: operator:<subject>` or
   `policy:<id>@<version>`.
5. Pushes a new branch and opens a pull request through the `ProviderClient`
   protocol, restricted to a repository allowlist. It never merges and
   never pushes a protected or default branch. A re-run that finds
   `vuoro-effect/<intent_id>` already carrying the same change is success.
6. Reports the outcome (`applied` with its acceptor, or `failed`) back
   through `IntentSource`. One intent's failure (checkout, apply, policy,
   commit, push, PR) is recorded and the others continue.

## Location independence

Where this package runs (a pod in a new `vuoro-effects` namespace, per the
pending trusted-service-boundary design) is not decided here. This package
takes its `IntentSource`, `ProviderClient` and signing key as constructor
arguments and makes no assumption about its deployment: no Kubernetes
manifest, no credential material and no network call to a real provider
lives in this repository.

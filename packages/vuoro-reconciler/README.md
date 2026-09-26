# vuoro-reconciler

The trusted-side executor for E3 (agentops#2467) effect intents. It is the
only thing in the E2/E3 design that applies a diff, signs a commit or opens
a pull request: `packages/vuoro-mcp-edge`'s `propose_effect` only records a
proposal and has no credential and no route to this package (see
`vuoro_mcp_edge.effect_tools`'s module docstring and
`docs/plans/2026-09-26-e2-e3-shared-contract.md` section 7).

## What it does

1. Polls accepted intents through the `IntentSource` protocol.
2. Clones (or reuses a working copy of) the intent's repository at a clean
   checkout of `base_commit` and applies `unified_diff`. A diff that does
   not apply cleanly is refused before anything is committed.
3. Commits the result, signed by the reconciler's own key (SSH or GPG,
   injected at runtime -- this package never mints or stores one), with
   trailers `Vuoro-Run: <run_id>` and `Vuoro-Intent: <intent_id>` linking
   the commit back to its originating run record.
4. Pushes a new branch and opens a pull request through the `ProviderClient`
   protocol, restricted to a repository allowlist. It never merges and
   never pushes a protected or default branch -- there is no method on
   `ProviderClient` that could do either, by construction.
5. Reports the outcome (`applied` or `failed`) back through `IntentSource`.

## Location independence

Where this package runs (a pod in a new `vuoro-effects` namespace, per the
pending trusted-service-boundary design) is not decided here. This package
takes its `IntentSource`, `ProviderClient` and signing key as constructor
arguments and makes no assumption about its deployment: no Kubernetes
manifest, no credential material and no network call to a real provider
lives in this repository.

# Vuoro Agent Guidance

Vuoro packages governed-work capabilities without taking ownership away from
their domain repositories.

## Ownership boundaries

- `packages/vuoro-client/` is transport-only. Do not add database drivers,
  migrations, domain adapters, authority implementations, or hard-coded domain
  commands.
- `packages/vuoro-service/` owns the reusable HTTP/process shell,
  compatibility gates, operational entrypoints, and composition of released
  adapters. Domain state machines and migration assets remain in their owner
  repositories and are consumed as pinned releases.
- `deploy/` will own neutral public packaging. Appservice-specific identities,
  addresses, credentials, CNPG resources, and production rollout policy do not
  belong here.
- Sprintctl, actionq, kctl, and auditctl retain their respective work,
  execution, knowledge, and audit semantics.

## Runtime invariants

- Service startup checks compatibility and never migrates automatically.
- Runtime roles do not execute DDL; migration roles do not serve requests.
- Catalog contents derive from the immutable service artifact and compatible
  adapters, never from deployment overlays.
- Generic development guards are reusable product behavior. Test-only HTTP
  endpoints and environment-specific application branches are forbidden.

## Verification

Run targeted package tests first, then the repository boundary gate:

```bash
uv run --package vuoro-client --extra test pytest packages/vuoro-client/tests
uv run --package vuoro-service --extra test pytest packages/vuoro-service/tests
uv build --package vuoro-client --wheel --out-dir dist/vuoro-client
uv build --package vuoro-service --wheel --out-dir dist/vuoro-service
uv run pytest
python /projects/dev/agentops/templates/dispatch/scripts/validate_verification_artifacts.py --root .
```

Inspect `vuoro.dispatch.json` risk surfaces before changing compatibility,
migration, identity, authority, invocation, or adapter-composition paths.

## Hybrid dispatch

This repository is hybrid-eligible: a frontier coordinator may freeze a bounded
`agentops-task/v1` packet and hand one disposable loop to a cheap OpenCode
worker (`bulk`, or `escalation` for one corrected retry). Workers hold no Git,
sprintctl, or deployment authority. Runbook:
`/projects/dev/agentops/docs/runbooks/hybrid-dispatch.md`.

The operator decides mode per item, before work starts:

| Item looks like | Mode |
|---|---|
| Tests, fixtures, or parametrization under `packages/*/tests/` or `tests/` | hybrid `bulk` |
| A refactor inside `packages/vuoro-service/src/` whose interface and acceptance the coordinator already fixed | hybrid `bulk` |
| Docs restating an already-decided contract | hybrid `bulk` |
| Deciding or changing client authority, schema compatibility, public semantics, or the transport-only boundary | coordinator-only |
| Client polling, long-poll, reconnect, or fixture mechanics against a frozen interface and acceptance packet | hybrid `bulk` |
| `deploy/`, packaging, or appservice-facing configuration | coordinator-only |
| Compatibility gates, migration assets, runtime-vs-migration role separation | coordinator-only |
| Catalog derivation, `pyproject.toml`, `uv.lock`, adapter composition | coordinator-only |
| Deciding *what* the behavior should be, rather than implementing a decided one | coordinator-only |

The coordinator owns decisions on every `risk_surface` marked
`required_on_change` in `vuoro.dispatch.json`. Protected paths prevent a
worker from changing contracts, composition, deployment, or repository
policy. A frozen packet may name transport-only client implementation paths
only when schemas, interfaces, fixtures, and acceptance are already fixed;
the coordinator must review the boundary and compatibility evidence.
`.agents/overlays/vuoro.hybrid-worker.md` carries the same boundaries and stop
conditions into the worker's context.

Gate every packet with a registered command from `hybrid.commands`
(`vuoro.client.tests`, `vuoro.service.tests`, `vuoro.boundaries`,
`vuoro.suite`). If no registered command can fail on a wrong answer, do not
dispatch — review cost will exceed the saving.

The current devbox hybrid dispatcher is one runner implementation, not the
architecture. Portable execution contracts are owner-staged in Actionq and
documented in `docs/architecture/portable-execution.md`. Vuoro may compose
released execution capabilities, but it does not own Sprintctl plans, Actionq
claims or leases, runner harness internals, candidate publication, or Auditctl
findings. Frozen worker packets carry no claim tokens or provider credentials;
candidate results become immutable Git artifacts before review or integration.

<!-- agentops-project-pointer:start -->
See `.agents/project.generated.md` for cross-repo project context (agentops-managed; do not hand-edit).
<!-- agentops-project-pointer:end -->

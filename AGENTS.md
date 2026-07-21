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

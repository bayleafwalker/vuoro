# Vuoro

[![pages](https://github.com/bayleafwalker/vuoro/actions/workflows/pages.yml/badge.svg)](https://github.com/bayleafwalker/vuoro/actions/workflows/pages.yml)

[Explore the Vuoro overview.](https://bayleafwalker.github.io/vuoro/)

Vuoro is a reusable governed-work substrate. It keeps machine-local effects on
the machine while serving shared work, execution, knowledge, and audit
capabilities through one versioned runtime.

## Project status

| | Status |
| --- | --- |
| **Current operational system** | The existing domain tools and agent-cockpit documented in [agentops](https://github.com/bayleafwalker/agentops). |
| **This repository** | The target transport-neutral client and deployable service composition layer. It packages capabilities without taking authority from their domain repositories. |
| **Works today** | Versioned handshake, catalog, and generic invocation protocol; enforced client/service packaging boundary; FastAPI service and transport-only client bootstrap; neutral Compose and Kustomize packaging checks. |
| **Still to complete** | Production-ready released adapters, environment deployment composition, and deliberate migration of request routing—not domain authority—behind the service boundary. |
| **Project overview** | [Vuoro on kotona.app](https://kotona.app/projects/vuoro/) |
| **Current implementation** | [agentops system map, cockpit, contracts, and operating walkthrough](https://github.com/bayleafwalker/agentops) |

This repository deliberately publishes five distributions:

- `vuoro-client` is transport-only. It owns endpoint and identity profiles,
  handshake/catalog discovery, schema rendering, caching, and generic
  invocation. Installing it must never install domain cores, database drivers,
  or migrations.
- `vuoro-service` is the deployable FastAPI/uvicorn runtime. It owns service
  composition, compatibility checks, migration entrypoints, and explicitly
  authorized administration commands.
- `vuoro-bootstrap` is the release-gated filesystem boundary for public
  onboarding. It consumes Cloud's device flow but does not own account,
  workspace, tenant, or domain state.
- `vuoro-schema-runtime` is the stdlib-only shared central-schema runtime. It
  supplies migration metadata and fail-closed compatibility checks without
  selecting a database driver or owning domain migrations.
- `vuoro-adapter-kit` is the stdlib-only adapter contract kit. It supplies
  strict JSON-Schema and operation-registration primitives without importing
  the service shell or any domain owner.

The bootstrap establishes the packaging boundary and protocol contract.
Released domain adapters and production deployment composition remain
separately reviewable work.

## Architecture at a glance

```text
agent or cockpit
       │
       ├── local mode ─────► owning CLI ─────► repo-local state and effects
       │
       └── served mode ────► vuoro-client ───► vuoro-service
                                                    │
                                                    ▼
                                      pinned owner adapters
                                                    │
                                      remote mode   ▼
                                  sprintctl · actionq · kctl · auditctl
                                      shared PostgreSQL authorities
```

Local, remote, and served describe communication paths, not competing owners.
The domain tools retain their state machines. Machine-local worktrees and
filesystem effects stay on the executing machine even when shared coordination
is served remotely. See the
[system shape and end-to-end walkthrough](https://github.com/bayleafwalker/agentops/blob/main/docs/architecture/vuoro-system-shape.md)
for the ownership map, failure rejection, and recovery path.

Commands may eventually return references to domain-owned observable
resources. Vuoro standardizes reference, snapshot, change, and delivery
envelopes while the owning domain retains lifecycle authority; see
[Domain-owned observable resources](docs/architecture/observable-resources.md).

The current devbox dispatcher is one implementation of governed execution,
not the placement contract. The staged portable boundary between Sprintctl
plans, Actionq lifecycle authority, interchangeable runners, immutable Git
candidate artifacts, Auditctl evidence, and Vuoro composition is defined in
[Portable governed execution](docs/architecture/portable-execution.md).

Command-output mediation is provided by the adjacent
[`outctl`](https://github.com/bayleafwalker/outctl) substrate repository. It
runs at the runner/harness boundary to retain recoverable raw stdout/stderr and
return deterministic bounded projections. Vuoro may advertise opaque capture
references, but it does not own raw logs, subprocess lifecycle, retention, or
projection policy.

## Development

Python 3.12 and `uv` are required.

```bash
uv sync --all-packages --all-extras
uv build --package vuoro-client --wheel --out-dir dist/vuoro-client
uv build --package vuoro-bootstrap --wheel --out-dir dist/vuoro-bootstrap
uv build --package vuoro-service --wheel --out-dir dist/vuoro-service
uv build --package vuoro-schema-runtime --wheel --out-dir dist/vuoro-schema-runtime
uv build --package vuoro-adapter-kit --wheel --out-dir dist/vuoro-adapter-kit
uv run pytest
```

The client and service can also be tested independently:

```bash
uv run --package vuoro-client --extra test pytest packages/vuoro-client/tests
uv run --package vuoro-bootstrap --extra test pytest packages/vuoro-bootstrap/tests
uv run --package vuoro-service --extra test pytest packages/vuoro-service/tests
```

See [`docs/architecture/packaging.md`](docs/architecture/packaging.md) for the
enforced dependency and ownership boundaries and
[`docs/architecture/protocol-v1.md`](docs/architecture/protocol-v1.md) for the
handshake, catalog, and generic invocation contract.
Adapter pinning, the required source evidence for a new Sprintctl work adapter,
and the release/operator boundary are documented in
[`docs/architecture/adapter-promotion.md`](docs/architecture/adapter-promotion.md).

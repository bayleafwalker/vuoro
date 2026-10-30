# Vuoro

[![pages](https://github.com/bayleafwalker/vuoro/actions/workflows/pages.yml/badge.svg)](https://github.com/bayleafwalker/vuoro/actions/workflows/pages.yml)

[Explore the Vuoro overview.](https://bayleafwalker.github.io/vuoro/)

Vuoro is a reusable governed-work substrate. It keeps machine-local effects on
the machine while serving shared work and audit
capabilities through one versioned runtime.

## Product direction

The [2026-10-15 owner direction](docs/direction/2026-10-15-product-direction.md)
packages shipped agentic products around four portable contracts: work identity,
evidence, authority and learning. Its provisioning ledger separates supplier
capabilities from Vuoro-owned semantics and defers new multi-tenant expansion.
This is product direction; the status below describes bounded implementation
and evidence.

## Project status

Current implementation follow-through:
[capture, continuation and bounded decision experiments](docs/plans/2026-10-30-agent-substrate-integration.md)
maps the October 30 tooling review into existing AgentOps, Sovite, Sprintctl
and Vuoro work packages. It is a contract/plan refinement, with explicit
qualification and recovery cases; runtime and deployment status remain below.

| | Status |
| --- | --- |
| **What it is** | A coordination and settlement substrate: leases, takeover, stale-result rejection with evidence retention, restart, and dependency release, served over a public MCP route. Domain authority stays with the owner repositories. |
| **Evidence it works** | The [settlement scenario](docs/evidence/2026-09-29-m1-4-settlement-scenario/README.md) (local 34/34; live: every scenario case passed, one cleanup expectation failed on a harness environment gap, since corrected) and the provider-neutral [conformance suite](packages/vuoro-service/tests/conformance/). The scenario used two scripted processes, not two commercial harnesses. |
| **Bounded MI-1 proof** | Real hosted Claude → isolated native Codex continuation, protected exact-artifact publication, interruption/recovery and independent authorization reconstruction are accepted. P1–P4 include a bounded completeness baseline with explicit capture/attribution failures. See [proof and limits](https://github.com/bayleafwalker/agentops/blob/main/docs/evidence/2026-10-09-mi1-bindery/README.md). |
| **Not yet established** | Whole-estate capture coverage, direct hosted owner registration, legacy Auditctl import (S4), hardware/ERH-006 repetition and unqualified resource-owner horizons. The original vendor comparison was retired by the operator; no comparative result is claimed. |
| **Delivered milestone** | [Market integration milestone MI-1](docs/plans/2026-10-05-market-integration-milestone.md): an end-to-end proof across real harnesses, provider evidence ingestion, a reconstruction view, and an evidence-completeness measure. |
| **Where current truth lives** | Accepted product sourcing and learning scope: [2026-10-15 direction](docs/direction/2026-10-15-product-direction.md). Product delivery dependencies and acceptance: [2026-10-17 plan](docs/plans/2026-10-17-product-delivery-plan.md) (accepted planning scope; implementation and qualification pending). Target state and its product amendment: agentops `docs/plans/2026-09-17-target-state.md`. Component status: [disposition register](docs/direction/disposition-register.yaml). Estate shape (a 2026-09-12 snapshot): [The agentic estate](docs/architecture/agentic-estate.md). Where older plans conflict, those win. |
| **Project overview** | [Vuoro on kotona.app](https://kotona.app/projects/vuoro/) |

Vuoro is one product with three surfaces and unchanged internal owners: **work
and evidence** (Sprintctl authority), **connectors** (MCP and thin
observation/import adapters), and **protected operations** (customer-controlled
acceptance and effect reconciliation). Native harnesses and providers execute;
Vuoro keeps work identity, authority and history intact when a worker changes
or is lost halfway through.

This repository deliberately publishes five distributions:

- `vuoro-client` is transport-only. It owns endpoint and identity profiles,
  handshake/catalog discovery, schema rendering, caching, and generic
  invocation. Installing it must never install domain cores, database drivers,
  or migrations.
- `vuoro-service` is the deployable FastAPI/uvicorn runtime. It owns service
  composition, compatibility checks, migration entrypoints, and explicitly
  authorized administration commands.
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
                                        sprintctl · auditctl
                                      shared PostgreSQL authorities
```

Local, remote, and served describe communication paths, not competing owners.
The domain tools retain their state machines. Machine-local worktrees and
filesystem effects stay on the executing machine even when shared coordination
is served remotely. See the
[system shape and end-to-end walkthrough](https://github.com/bayleafwalker/agentops/blob/main/docs/architecture/vuoro-system-shape.md)
for the ownership map, failure rejection, and recovery path. The ratified
record of the surrounding estate — control plane, authority owners, execution
hosts and identities, trust boundary, evidence stores, and the ownership table
that supersedes the historical one — is
[The agentic estate](docs/architecture/agentic-estate.md).

Commands may eventually return references to domain-owned observable
resources. Vuoro standardizes reference, snapshot, change, and delivery
envelopes while the owning domain retains lifecycle authority; see
[Domain-owned observable resources](docs/architecture/observable-resources.md).

Future governed execution uses product-native runtimes directly. ActionQ is a
thin federation layer for external execution references, binding assurance,
acceptance, and reconciliation; it is not the future owner of a daemon, queue,
leases, runner, or fan-out engine. Sprintctl reservations are advisory
coordination rather than exclusive execution claims. Vuoro only transports
and composes the released owner capabilities. See
[Native-runtime and execution-federation alignment](docs/plans/2026-08-20-execution-federation-alignment.md).

Raw runtime output may remain host-local. Git holds authored artifacts,
Auditctl holds durable findings, and ActionQ may retain bounded evidence
references alongside an external execution. Outctl is not a required Vuoro
member or service dependency. The earlier runner/Outctl design is preserved
as historical planning in
[Portable governed execution](docs/architecture/portable-execution.md).

## Development

Python 3.12 and `uv` are required.

```bash
uv sync --all-packages --all-extras
uv build --package vuoro-client --wheel --out-dir dist/vuoro-client
uv build --package vuoro-service --wheel --out-dir dist/vuoro-service
uv build --package vuoro-schema-runtime --wheel --out-dir dist/vuoro-schema-runtime
uv build --package vuoro-adapter-kit --wheel --out-dir dist/vuoro-adapter-kit
uv run pytest
```

The client and service can also be tested independently:

```bash
uv run --package vuoro-client --extra test pytest packages/vuoro-client/tests
uv run --package vuoro-service --extra test pytest packages/vuoro-service/tests
uv run --package vuoro-mcp-edge --extra test pytest packages/vuoro-mcp-edge/tests
```

### Run it locally

To **evaluate settlement without a Cloud account**, use the optional
[disposable native settlement demo](packages/vuoro-demo/README.md). It has a
source-pinned install path and a separate `vuoro` entrypoint; the current source
also supplies `vuoro doctor`. It starts its own disposable PG16 cluster and
loopback service, demonstrates takeover, late-result refusal, verification and
dependency release, then removes fixture state after confirmed shutdown.
Python 3.12+, PostgreSQL 16 tools, Git and a non-root Unix user are required.
This is an evaluation consumer, not a persistent Vuoro Local installation.

The installed console scripts are `vuoro-service` and `vuoro-client` (the
client's own `--help` reports its argparse prog name as `vuoro`):

```console
$ uv run --package vuoro-service vuoro-service --help
usage: vuoro-service [-h] [--version] {serve,mcp-serve} ...
$ uv run --package vuoro-client vuoro-client --help
usage: vuoro [-h] [--version] {recovery} ...
```

The retained offline recovery namespace is export-only. See the
[native incident recovery runbook](docs/architecture/native-recovery.md) for
private local storage, interrupted append handling and explicit owner review.

`vuoro-service mcp-serve --port 8081` runs the MCP protocol server for hosted
runtimes; it needs `packages/vuoro-mcp-edge` installed (the image has it) and
is described in [`packages/vuoro-mcp-edge/README.md`](packages/vuoro-mcp-edge/README.md).

For a local client/service/PostgreSQL evaluation stack, see
[`deploy/compose/README.md`](deploy/compose/README.md). That Compose stack is
a disposable local packaging check, not a `vuoro-dev` deployment, and must not
receive production endpoints, credentials, mounts, or identities.

The hosted pilot is [vuoro.cloud](https://vuoro.cloud/). Its dated recordings,
illustrations and local proofs describe different scopes; source availability
does not establish a hosted deployment. The
[operational adoption plan](docs/plans/2026-10-16-operational-adoption.md) covers
onboarding, thin adapters, acceptance checks, self-hosting/portability and
visibility against current owner direction.

See [`docs/architecture/packaging.md`](docs/architecture/packaging.md) for the
enforced dependency and ownership boundaries and
[`docs/architecture/protocol-v1.md`](docs/architecture/protocol-v1.md) for the
handshake, catalog, and generic invocation contract.
Adapter pinning, the required source evidence for a new Sprintctl work adapter,
and the release/operator boundary are documented in
[`docs/architecture/adapter-promotion.md`](docs/architecture/adapter-promotion.md).

## Goal-derived delivery roadmap

The [KAOS roadmap](docs/plans/2026-10-18-kaos-roadmap.md) traces ecosystem goals to this project's implementation, product onboarding, dependencies and acceptance work. It records planned scope, not deployed capability.

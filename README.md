# Vuoro

Vuoro is a reusable governed-work substrate. It keeps machine-local effects on
the machine while serving shared work, execution, knowledge, and audit
capabilities through one versioned runtime.

This repository deliberately publishes two distributions:

- `vuoro-client` is transport-only. It owns endpoint and identity profiles,
  handshake/catalog discovery, schema rendering, caching, and generic
  invocation. Installing it must never install domain cores, database drivers,
  or migrations.
- `vuoro-service` is the deployable FastAPI/uvicorn runtime. It owns service
  composition, compatibility checks, migration entrypoints, and explicitly
  authorized administration commands.

The first bootstrap establishes that packaging boundary. Protocol contracts,
domain adapters, and deployment packaging land in separately reviewable work.

## Development

Python 3.12 and `uv` are required.

```bash
uv sync --all-packages --all-extras
uv build --package vuoro-client --wheel --out-dir dist/vuoro-client
uv build --package vuoro-service --wheel --out-dir dist/vuoro-service
uv run pytest
```

The client and service can also be tested independently:

```bash
uv run --package vuoro-client --extra test pytest packages/vuoro-client/tests
uv run --package vuoro-service --extra test pytest packages/vuoro-service/tests
```

See [`docs/architecture/packaging.md`](docs/architecture/packaging.md) for the
enforced dependency and ownership boundaries and
[`docs/architecture/protocol-v1.md`](docs/architecture/protocol-v1.md) for the
handshake, catalog, and generic invocation contract.

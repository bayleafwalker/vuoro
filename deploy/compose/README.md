# Local Compose evaluation

Use this stack with disposable local data and identities. It requires Docker
with the Compose plugin and runs PostgreSQL 16. The service listens on loopback. Use a fresh Compose
project name for each independent evaluation.

From the repository root, copy `deploy/compose/identities.example.json` to a
private temporary directory. Replace the example token with a unique local
evaluation token and set its repository ID to `local-evaluation`. The identity
file must be readable by container UID 65532. Keep its parent directory private.

```bash
export VUORO_COMPOSE_IDENTITIES_FILE=/absolute/path/to/identities.json
export COMPOSE_PROJECT_NAME=vuoro-local-evaluation
docker compose -f deploy/compose/compose.yaml up -d --wait postgres
docker compose -f deploy/compose/compose.yaml run --rm migrate
docker compose -f deploy/compose/compose.yaml up -d vuoro-service
curl --fail http://127.0.0.1:8080/api/meta/v1/handshake
```

The one-off migration service runs the Work, Audit, and Knowledge owner
entrypoints with separate migration roles. The HTTP service starts after that
explicit step and receives only runtime-role connections. Its startup checks
owner compatibility before serving requests. PostgreSQL and Knowledge objects
use separate local volumes. The sample identity is read-only.

By default Compose builds the service image from this checkout. To evaluate a
published release, first pull its immutable image digest, set
`VUORO_COMPOSE_IMAGE` to that reference, then pass `--no-build` to the service
`up` command. Set `VUORO_COMPOSE_PORT` if the default port is already in use.

Stop the stack with `docker compose -f deploy/compose/compose.yaml down`.
For a disposable project whose database and Knowledge objects can be deleted,
run the same command with `--volumes`. Preserve any evaluation evidence you
need before deleting those volumes.

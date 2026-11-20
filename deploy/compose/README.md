# Local Compose evaluation

Use this stack with disposable local data and identities. It requires Docker
with the Compose plugin and runs PostgreSQL 16. The service listens on loopback. Use a fresh Compose
project name for each independent evaluation.

From the repository root, copy `deploy/compose/identities.example.json` to a
private temporary directory. Replace the example token with a unique local
evaluation token and set its repository ID to `local-evaluation`. The identity
file must be readable by container UID 65532. Keep its parent directory private.

```bash
# Keep this directory across restarts and protect it like the identity file.
config_dir="$HOME/.config/vuoro-compose"
install -d -m 700 "$config_dir"
(
  umask 077
  set -eC
  for role in ADMIN WORK_MIGRATION WORK_RUNTIME AUDIT_MIGRATION AUDIT_RUNTIME KNOWLEDGE_MIGRATION KNOWLEDGE_RUNTIME; do
    password=$(openssl rand -hex 32)
    printf 'VUORO_COMPOSE_%s_PASSWORD=%s\n' "$role" "$password"
  done > "$config_dir/database.env"
)
set -a
. "$config_dir/database.env"
set +a
export VUORO_COMPOSE_IDENTITIES_FILE=/absolute/path/to/identities.json
export COMPOSE_PROJECT_NAME=vuoro-local-evaluation
docker compose -f deploy/compose/compose.yaml up -d --wait postgres
docker compose -f deploy/compose/compose.yaml run --rm migrate
docker compose -f deploy/compose/compose.yaml up -d vuoro-service
curl --fail --retry 10 --retry-all-errors http://127.0.0.1:8080/api/meta/v1/handshake
```

Generate the password file once per Compose project. Use hexadecimal secrets
so they can be embedded in the database connection URLs without escaping.
Load the same file for subsequent Compose commands. The init script uses
these passwords only when PostgreSQL initializes an empty volume. For an
existing evaluation with disposable data, initialize a new project and volume.
For retained data, coordinate database role password changes before switching
the Compose environment. Restrict Docker daemon access to trusted operators
because container environments contain credentials.

The one-off migration service runs the Work, Audit, and Knowledge owner
entrypoints with separate migration roles. The HTTP service starts after that
explicit step and receives only runtime-role connections. Its startup checks
owner compatibility before serving requests. PostgreSQL and Knowledge objects
use separate local volumes. The sample identity is read-only.

By default Compose builds the service image from this checkout. To evaluate a
published release, first pull its immutable image digest, set
`VUORO_COMPOSE_IMAGE` to that reference, then run migration with `run --pull never --rm migrate` and start the service
with `up -d --no-build vuoro-service`. Set `VUORO_COMPOSE_PORT` if the default port is already in use.

Stop the stack with `docker compose -f deploy/compose/compose.yaml down`.
For a disposable project whose database and Knowledge objects can be deleted,
run the same command with `--volumes`. Preserve any evaluation evidence you
need before deleting those volumes.

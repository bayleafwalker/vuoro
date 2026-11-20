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

## Back up and restore a Compose workspace

Use the same Vuoro image digest and PostgreSQL major version for the initial
restore. Keep the identity configuration and the selected image digest separately under
operator control.
These commands assume the Compose database and Knowledge volume have no
writers other than the service.

Stop the service before capturing both files. Keep the backup private because
it contains workspace data. Run these commands from the repository root with
the source project environment loaded.

```bash
umask 077
backup_root="$HOME/.local/state/vuoro-backups"
install -d -m 700 "$backup_root"
backup=$(mktemp -d "$backup_root/workspace.XXXXXXXX")
compose() { docker compose -f deploy/compose/compose.yaml "$@"; }

compose stop vuoro-service &&
compose exec -T postgres pg_dump -U vuoro_admin -d vuoro --format=custom   > "$backup/workspace.dump" &&
compose run --pull never --no-deps --rm -T --user 0 --entrypoint tar \
  vuoro-service -C /tmp -cf - knowledge-objects > "$backup/knowledge.tar" &&
(cd "$backup" && sha256sum workspace.dump knowledge.tar > SHA256SUMS)
```

Treat the backup as complete only after all commands succeed and
`SHA256SUMS` exists. Restart the source with
`compose up -d --no-build vuoro-service` when you are ready.

Restore into a fresh project and unused port with the service stopped. Load
the target project's database passwords and identity configuration before
running these commands. Keep each repository ID and retained principal ID
consistent with the source. The database bootstrap creates the target roles
with their target passwords and the restore preserves schema ownership and
grants. Use a backup you created or received through a trusted channel. The
preflight checks the bundle structure and checksums. Use Python 3.11 or newer
and keep the backup directory private until both restore commands finish.

```bash
export COMPOSE_PROJECT_NAME=vuoro-restore
export VUORO_COMPOSE_PORT=8081
# Set backup to the absolute path stored in the backup variable in the source shell.
python3 deploy/compose/verify-backup.py "$backup" &&
compose up -d --wait postgres &&
compose exec -T postgres pg_restore -U vuoro_admin -d vuoro   --clean --if-exists --single-transaction < "$backup/workspace.dump" &&
compose run --pull never --no-deps --rm -T --user 0 --entrypoint tar   vuoro-service -C /tmp -xpf - < "$backup/knowledge.tar" &&
compose up -d --no-build vuoro-service &&
curl --fail --retry 10 --retry-all-errors http://127.0.0.1:8081/api/meta/v1/handshake
```

After startup, use an authorized client to compare a known task and Knowledge
object with the source backup. If any restore step fails, keep that target
offline and retry with fresh target volumes.

## Upgrade a running Compose workspace

Prepare the new image and a complete backup before the upgrade. Record the
currently running image digest and keep both images locally available. Use
the same Compose project, port, identities, and database credentials. This
procedure keeps the PostgreSQL major version and both volumes in place.

Check the new and prior service versions in the
[tenant runtime schema map](../../packages/vuoro-service/src/vuoro_service/tenant-runtime-schema-map.json).
When the Work schema target changes, determine whether the prior image
accepts the target schema. Check Audit and Knowledge compatibility as well.
Use the backup recovery procedure if the prior image cannot serve the
upgraded data.

With the source stopped after the backup, set `next_image` to the verified
immutable image reference and run the migration job before starting traffic.

```bash
export VUORO_COMPOSE_IMAGE="$next_image"
compose run --pull never --rm migrate &&
compose up -d --no-build --pull never vuoro-service &&
curl --fail --retry 10 --retry-all-errors \
  "http://127.0.0.1:${VUORO_COMPOSE_PORT:-8080}/api/meta/v1/handshake"
```

Confirm the handshake names the expected service version, then read a known
task or sprint through an authenticated client before resuming writes. For a
compatible image rollback, stop the service, set `VUORO_COMPOSE_IMAGE` to the
prior digest, and repeat the start and read checks. If a migration or readback
fails, keep traffic stopped while you determine whether to roll back the image
or restore the backup into a fresh project. Rolling back the image does not
revert database changes.

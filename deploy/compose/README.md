# Local Compose evaluation

This stack is a disposable, local packaging check. It is not a `vuoro-dev`
deployment and it must not receive production endpoints, credentials, mounts,
or identities.

Create a private identity registry from `identities.example.json`, replacing
the example token with a unique value. Then set its path and start PostgreSQL:

```bash
export VUORO_COMPOSE_IDENTITIES_FILE="$PWD/deploy/compose/identities.local.json"
docker compose -f deploy/compose/compose.yaml up --build postgres
```

Before starting `vuoro-service`, run the four owner migration entrypoints with
their **migration** roles. The runtime container receives only the four
runtime-role DSNs and startup never performs DDL. Owner-specific migration
arguments and evidence requirements remain authoritative; the neutral
Kustomize templates list the same four entrypoints.

After compatibility checks pass, start the service with:

```bash
docker compose -f deploy/compose/compose.yaml up --build vuoro-service
```

The known local password exists solely inside this throwaway Compose stack.
It is deliberately not used by the public Kustomize base or any environment
record.
